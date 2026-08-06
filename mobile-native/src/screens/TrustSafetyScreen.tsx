import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  createSupportTicket,
  listSupportTickets,
  loadCachedSupportState,
  openSupportWebFallback,
  scanScamShield,
  ScamShieldResult,
  submitSecurityReport,
  SupportState
} from "../api/support";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { createThemedStyles } from "../theme/themedStyles";

type Props =
  | NativeStackScreenProps<RootStackParamList, "TrustSafety">
  | NativeStackScreenProps<RootStackParamList, "TrustSafetySupport">
  | NativeStackScreenProps<RootStackParamList, "TrustSafetyHelp">
  | NativeStackScreenProps<RootStackParamList, "TrustCenter">
  | NativeStackScreenProps<RootStackParamList, "SecurityReport">
  | NativeStackScreenProps<RootStackParamList, "ScamShield">;

const issueTypes = ["account", "safety", "payments", "notifications", "creator", "marketplace"];
const reportTypes = ["account_compromise", "phishing", "abuse", "scam", "privacy", "other"];

export function TrustSafetyScreen({ navigation, route }: Props) {
  const { authState } = useAuth();
  // Wrong-subject guard: support tickets and security reports belong to the
  // signed-in viewer. If the route params name another profile as the subject,
  // this screen must refuse instead of showing the viewer's private history.
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);
  const [state, setState] = useState<SupportState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [supportEmail, setSupportEmail] = useState("");
  const [supportIssue, setSupportIssue] = useState("safety");
  const [supportSubject, setSupportSubject] = useState("PulseSoc native support request");
  const [supportMessage, setSupportMessage] = useState("");
  const [reportEmail, setReportEmail] = useState("");
  const [reportType, setReportType] = useState("phishing");
  const [reportTarget, setReportTarget] = useState("");
  const [reportDescription, setReportDescription] = useState("");
  const [scanText, setScanText] = useState("");
  const [scanResult, setScanResult] = useState<ScamShieldResult | null>(null);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await listSupportTickets();
      setState(next);
    } catch (loadError) {
      const cached = await loadCachedSupportState();
      if (cached) {
        setState(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Trust and Safety could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    navigation.setOptions({ title: "Trust & Safety" });
  }, [navigation]);

  useEffect(() => {
    // Owner-only fetch: skip entirely on a visitor route (no fetch-then-hide).
    if (!routeContext.isOwnProfile) return;
    load("initial").catch(() => undefined);
  }, [load, routeContext.isOwnProfile]);

  async function submitSupport() {
    if (!supportEmail.trim() || !supportMessage.trim()) {
      setError("Enter a support email and message.");
      setNotice("");
      return;
    }
    setBusy("support");
    setError("");
    setNotice("");
    try {
      const result = await createSupportTicket({
        email: supportEmail.trim(),
        issue_type: supportIssue,
        subject: supportSubject.trim() || supportIssue,
        message: supportMessage.trim()
      });
      setSupportMessage("");
      setNotice(result.message || `Support ticket #${result.ticket_id || ""} opened.`);
      await load("refresh");
    } catch (supportError) {
      setError(supportError instanceof Error ? supportError.message : "Support ticket could not be opened.");
    } finally {
      setBusy("");
    }
  }

  async function submitReport() {
    if (!reportDescription.trim()) {
      setError("Describe the security concern before submitting.");
      setNotice("");
      return;
    }
    setBusy("security-report");
    setError("");
    setNotice("");
    try {
      const result = await submitSecurityReport({
        email: reportEmail.trim(),
        report_type: reportType,
        target: reportTarget.trim(),
        description: reportDescription.trim()
      });
      setReportDescription("");
      setNotice(result.message || `Security report #${result.report_id || ""} received.`);
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : "Security report could not be submitted.");
    } finally {
      setBusy("");
    }
  }

  async function runScan() {
    if (!scanText.trim()) {
      setError("Paste a suspicious message, URL, token, or wallet address first.");
      setNotice("");
      return;
    }
    setBusy("scan");
    setError("");
    setNotice("");
    try {
      const result = await scanScamShield(scanText.trim());
      setScanResult(result);
      setNotice(result.message || "Scam Shield scan complete.");
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "Scam Shield scan failed.");
    } finally {
      setBusy("");
    }
  }

  // Visitor destination with no visitor variant: refuse rather than render the
  // viewer's tickets under someone else's name. All hooks have already run.
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
        <Text style={styles.centerText}>Loading Trust & Safety</Text>
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
        <Text style={styles.eyebrow}>PulseSoc safety grid</Text>
        <Text style={styles.title}>Trust & Safety</Text>
        <Text style={styles.subtitle}>
          {offline ? "Showing cached support history. Pull to reconnect." : "Native access to support, scam scanning, security reporting, and trusted policy routes."}
        </Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <Panel>
        <View style={styles.heroRow}>
          <View>
            <Text style={styles.panelTitle}>Protection status</Text>
            <Text style={styles.muted}>Reports, tickets, scans, and moderation stay server-authoritative.</Text>
          </View>
          <Text style={styles.signal}>{state?.tickets.length || 0}</Text>
        </View>
        <View style={styles.buttonGrid}>
          <ActionButton label="Trust Center" variant="secondary" onPress={() => openSupportWebFallback("/trust-center")} />
          <ActionButton label="Rules" variant="secondary" onPress={() => openSupportWebFallback("/community-rules")} />
          <ActionButton label="Web Help" variant="secondary" onPress={() => openSupportWebFallback("/pulse/help")} />
          <ActionButton label="Verification" variant="secondary" onPress={() => navigation.navigate("VerificationCenter", { title: "Verification Center" })} />
          <ActionButton label="Account Health" variant="secondary" onPress={() => navigation.navigate("AccountHealth", { title: "Account Health" })} />
          <ActionButton label="Safety Hub" variant="secondary" onPress={() => navigation.navigate("SafetyHub", { title: "Safety Hub" })} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Support tickets</Text>
        {(state?.tickets || []).slice(0, 5).map((ticket) => (
          <View key={ticket.id} style={styles.ticketRow}>
            <Text style={styles.ticketTitle}>#{ticket.id} · {ticket.subject}</Text>
            <Text style={styles.muted}>{ticket.issue_type} · {ticket.status} · {formatShortTime(ticket.updated_at || ticket.created_at)}</Text>
          </View>
        ))}
        {!state?.tickets.length ? <Text style={styles.muted}>No support tickets returned by the backend.</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Open support ticket</Text>
        <TextInput
          accessibilityLabel="Support email"
          autoCapitalize="none"
          inputMode="email"
          onChangeText={setSupportEmail}
          placeholder="Email for support follow-up"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={supportEmail}
        />
        <ChoiceRow options={issueTypes} value={supportIssue} onChange={setSupportIssue} />
        <TextInput
          accessibilityLabel="Support subject"
          onChangeText={setSupportSubject}
          placeholder="Subject"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={supportSubject}
        />
        <TextInput
          accessibilityLabel="Support message"
          multiline
          onChangeText={setSupportMessage}
          placeholder="Describe what happened."
          placeholderTextColor={colors.muted}
          style={[styles.input, styles.textArea]}
          value={supportMessage}
        />
        <ActionButton label={busy === "support" ? "Opening" : "Open ticket"} disabled={busy === "support"} onPress={submitSupport} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Security report</Text>
        <ChoiceRow options={reportTypes} value={reportType} onChange={setReportType} />
        <TextInput
          accessibilityLabel="Security report email"
          autoCapitalize="none"
          inputMode="email"
          onChangeText={setReportEmail}
          placeholder="Optional email"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={reportEmail}
        />
        <TextInput
          accessibilityLabel="Security report target"
          onChangeText={setReportTarget}
          placeholder="URL, username, wallet, listing, or message"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={reportTarget}
        />
        <TextInput
          accessibilityLabel="Security report description"
          multiline
          onChangeText={setReportDescription}
          placeholder="Explain the risk. Never paste private keys, seed phrases, passwords, or one-time codes."
          placeholderTextColor={colors.muted}
          style={[styles.input, styles.textArea]}
          value={reportDescription}
        />
        <ActionButton label={busy === "security-report" ? "Submitting" : "Submit security report"} disabled={busy === "security-report"} onPress={submitReport} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Scam Shield</Text>
        <Text style={styles.muted}>Scan suspicious messages, URLs, token pitches, or wallet prompts using the existing PulseSoc safety engine.</Text>
        <TextInput
          accessibilityLabel="Scam Shield input"
          multiline
          onChangeText={setScanText}
          placeholder="Paste suspicious text here."
          placeholderTextColor={colors.muted}
          style={[styles.input, styles.textArea]}
          value={scanText}
        />
        <ActionButton label={busy === "scan" ? "Scanning" : "Scan risk"} disabled={busy === "scan"} onPress={runScan} />
        {scanResult ? <ScamResult result={scanResult} /> : null}
      </Panel>
    </ScrollView>
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

function ScamResult({ result }: { result: ScamShieldResult }) {
  const flags = result.red_flags || [];
  const actions = result.safe_actions || [];
  return (
    <View style={styles.scanResult}>
      <Text style={styles.scanRisk}>{result.risk_level || "Risk"} · {Number(result.risk_score || 0)}%</Text>
      <Text style={styles.muted}>{result.summary || result.response || result.message || "Scam Shield returned a result."}</Text>
      {flags.slice(0, 4).map((flag, index) => <Text key={`flag-${index}-${String(flag)}`} style={styles.listItem}>- {String(flag)}</Text>)}
      {actions.slice(0, 4).map((action, index) => <Text key={`action-${index}-${String(action)}`} style={styles.safeItem}>- {String(action)}</Text>)}
    </View>
  );
}

function ActionButton({
  label,
  onPress,
  disabled,
  variant = "primary"
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary";
}) {
  return (
    <Pressable accessibilityRole="button" disabled={disabled} style={[styles.actionButton, variant === "secondary" && styles.secondaryButton, disabled && styles.disabled]} onPress={onPress}>
      <Text style={[styles.actionText, variant === "secondary" && styles.secondaryText]}>{label}</Text>
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  content: {
    gap: 14,
    padding: 16
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
  header: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    padding: 16
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  panelTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  muted: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  heroRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between"
  },
  signal: {
    color: colors.accent,
    fontSize: 34,
    fontWeight: "900"
  },
  buttonGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  actionButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryButton: {
    backgroundColor: "transparent",
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth
  },
  disabled: {
    opacity: 0.55
  },
  actionText: {
    color: "#08110f",
    fontWeight: "900"
  },
  secondaryText: {
    color: colors.text
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
  textArea: {
    minHeight: 94,
    textAlignVertical: "top"
  },
  choiceRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  choicePill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 11
  },
  choicePillActive: {
    backgroundColor: "rgba(37,208,167,0.16)",
    borderColor: colors.accent
  },
  choiceText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  choiceTextActive: {
    color: colors.text
  },
  ticketRow: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 10
  },
  ticketTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  scanResult: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    padding: 10
  },
  scanRisk: {
    color: colors.warning,
    fontSize: 16,
    fontWeight: "900"
  },
  listItem: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  safeItem: {
    color: colors.accent,
    fontSize: 13,
    lineHeight: 19
  },
  error: {
    backgroundColor: "rgba(255,107,107,0.12)",
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.danger,
    padding: 10
  },
  notice: {
    backgroundColor: "rgba(37,208,167,0.1)",
    borderColor: colors.accent,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.accent,
    padding: 10
  }
}));
