import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import {
  AccountSettings,
  AccountState,
  disableTwoFactor,
  enableTwoFactor,
  generateRecoveryCodes,
  loadAccountState,
  loadCachedAccountState,
  openAccountWebFallback,
  reauthenticate,
  removeTrustedDevice,
  revokeAllSessions,
  updateAccountSettings,
  verifyEmail,
  verifyPhone
} from "../api/account";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = NativeStackScreenProps<RootStackParamList, "AccountCenter">;

type AccountSection = "account" | "security" | "privacy" | "devices";

const sections: { key: AccountSection; label: string }[] = [
  { key: "account", label: "Account" },
  { key: "security", label: "Security" },
  { key: "privacy", label: "Privacy" },
  { key: "devices", label: "Devices" }
];

const settingOptions: Record<string, { label: string; value: string }[]> = {
  profile_visibility: [
    { label: "Public", value: "public" },
    { label: "Private", value: "private" }
  ],
  message_requests: [
    { label: "Everyone", value: "everyone" },
    { label: "Followers", value: "followers" },
    { label: "None", value: "none" }
  ],
  notifications_enabled: [
    { label: "Enabled", value: "true" },
    { label: "Disabled", value: "false" }
  ],
  status_replies: [
    { label: "Everyone", value: "everyone" },
    { label: "Followers", value: "followers" },
    { label: "None", value: "none" }
  ],
  ads_personalization: [
    { label: "Allowed", value: "true" },
    { label: "Off", value: "false" }
  ],
  sci_fi_intensity: [
    { label: "Low", value: "low" },
    { label: "Medium", value: "medium" },
    { label: "High", value: "high" }
  ],
  reduced_motion: [
    { label: "System", value: "system" },
    { label: "Reduce", value: "true" },
    { label: "Full motion", value: "false" }
  ],
  language: [
    { label: "English", value: "en" },
    { label: "Spanish", value: "es" },
    { label: "French", value: "fr" },
    { label: "Haitian Creole", value: "ht" },
    { label: "Portuguese", value: "pt" }
  ]
};

export function AccountCenterScreen({ route, navigation }: Props) {
  const initialSection = normalizeSection(route.params?.section);
  const [section, setSection] = useState<AccountSection>(initialSection);
  const [state, setState] = useState<AccountState | null>(null);
  const [draftSettings, setDraftSettings] = useState<AccountSettings>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const title = useMemo(() => sectionTitle(section), [section]);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    setNotice("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await loadAccountState();
      setState(next);
      setDraftSettings(next.settings);
    } catch (loadError) {
      const cached = await loadCachedAccountState();
      if (cached) {
        setState(cached);
        setDraftSettings(cached.settings);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Account Center could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    setSection(normalizeSection(route.params?.section));
  }, [route.params?.section]);

  useEffect(() => {
    navigation.setOptions({ title });
  }, [navigation, title]);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  async function runAction(label: string, action: () => Promise<{ message?: string }>, options: { refresh?: boolean; codes?: boolean } = {}) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const result = await action();
      if (options.codes && "recovery_codes" in result) {
        const codes = Array.isArray(result.recovery_codes) ? result.recovery_codes.join("\n") : "";
        Alert.alert("Recovery codes", codes || result.message || "Recovery codes generated.");
      }
      setNotice(result.message || "Account security updated.");
      if (options.refresh !== false) await load("refresh");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Action could not be completed.");
    } finally {
      setBusy("");
    }
  }

  async function saveSettings() {
    setBusy("save-settings");
    setError("");
    setNotice("");
    try {
      const result = await updateAccountSettings(draftSettings);
      setDraftSettings(result.settings || draftSettings);
      await load("refresh");
      setNotice(result.message || "Settings saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Settings could not be saved.");
    } finally {
      setBusy("");
    }
  }

  function confirmTrustedDeviceRemoval(deviceId: number) {
    Alert.alert("Remove trusted device?", "This device will need to authenticate again before sensitive account actions.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove",
        style: "destructive",
        onPress: () => runAction(`remove-device:${deviceId}`, () => removeTrustedDevice(deviceId))
      }
    ]);
  }

  function confirmRevokeSessions() {
    Alert.alert("Sign out other sessions?", "PulseSoc will revoke other active sessions using the existing backend session controls.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Revoke",
        style: "destructive",
        onPress: () => runAction("revoke-sessions", revokeAllSessions)
      }
    ]);
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading Account Center</Text>
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
        <Text style={styles.eyebrow}>PulseSoc identity core</Text>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.subtitle}>
          {offline ? "Showing the last trusted snapshot. Pull to reconnect." : "Server-authoritative account, security, privacy, and device controls."}
        </Text>
      </View>

      <View style={styles.sectionTabs}>
        {sections.map((item) => (
          <Pressable
            accessibilityRole="button"
            key={item.key}
            style={[styles.sectionTab, section === item.key && styles.sectionTabActive]}
            onPress={() => setSection(item.key)}
          >
            <Text style={[styles.sectionTabText, section === item.key && styles.sectionTabTextActive]}>{item.label}</Text>
          </Pressable>
        ))}
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      {section === "account" ? (
        <AccountSectionView
          state={state}
          draftSettings={draftSettings}
          busy={busy}
          onDraftChange={(key, value) => setDraftSettings((current) => ({ ...current, [key]: value }))}
          onSave={saveSettings}
          onOpenWeb={openAccountWebFallback}
        />
      ) : null}

      {section === "security" ? (
        <SecuritySectionView
          state={state}
          busy={busy}
          onVerifyEmail={() => runAction("verify-email", verifyEmail)}
          onVerifyPhone={() => runAction("verify-phone", verifyPhone)}
          onEnableTwoFactor={() => runAction("enable-2fa", enableTwoFactor)}
          onDisableTwoFactor={() => runAction("disable-2fa", disableTwoFactor)}
          onGenerateRecoveryCodes={() => runAction("recovery-codes", generateRecoveryCodes, { codes: true })}
          onReauthenticate={() => runAction("reauthenticate", reauthenticate, { refresh: false })}
          onRevokeSessions={confirmRevokeSessions}
          onOpenWeb={openAccountWebFallback}
        />
      ) : null}

      {section === "privacy" ? (
        <PrivacySectionView
          draftSettings={draftSettings}
          busy={busy}
          onDraftChange={(key, value) => setDraftSettings((current) => ({ ...current, [key]: value }))}
          onSave={saveSettings}
          onOpenWeb={openAccountWebFallback}
        />
      ) : null}

      {section === "devices" ? (
        <DevicesSectionView state={state} busy={busy} onRemove={confirmTrustedDeviceRemoval} onOpenWeb={openAccountWebFallback} />
      ) : null}
    </ScrollView>
  );
}

function AccountSectionView({
  state,
  draftSettings,
  busy,
  onDraftChange,
  onSave,
  onOpenWeb
}: {
  state: AccountState | null;
  draftSettings: AccountSettings;
  busy: string;
  onDraftChange: (key: string, value: string) => void;
  onSave: () => void;
  onOpenWeb: (path?: string) => void;
}) {
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>Account status</Text>
        <View style={styles.metricGrid}>
          <Metric label="Plan" value={state?.status.access_label || state?.status.subscription_plan || "Free"} />
          <Metric label="Language" value={(state?.status.preferred_language || state?.status.language || "en").toUpperCase()} />
          <Metric label="Telegram" value={state?.status.telegram_linked ? "Linked" : "Not linked"} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Experience controls</Text>
        <SettingChoice label="Motion" settingKey="reduced_motion" value={draftSettings.reduced_motion} onChange={onDraftChange} />
        <SettingChoice label="Visual intensity" settingKey="sci_fi_intensity" value={draftSettings.sci_fi_intensity} onChange={onDraftChange} />
        <SettingChoice label="Language" settingKey="language" value={draftSettings.language} onChange={onDraftChange} />
        <ActionButton label={busy === "save-settings" ? "Saving" : "Save account settings"} disabled={busy === "save-settings"} onPress={onSave} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Credentials and data</Text>
        <Text style={styles.muted}>Password changes, account export, and account deletion stay on protected PulseSoc web flows until dedicated native reauth UX is planned.</Text>
        <View style={styles.buttonGrid}>
          <ActionButton label="Password and email" variant="secondary" onPress={() => onOpenWeb("/account/settings")} />
          <ActionButton label="Delete account" variant="danger" onPress={() => onOpenWeb("/account/delete")} />
        </View>
      </Panel>
    </>
  );
}

function SecuritySectionView({
  state,
  busy,
  onVerifyEmail,
  onVerifyPhone,
  onEnableTwoFactor,
  onDisableTwoFactor,
  onGenerateRecoveryCodes,
  onReauthenticate,
  onRevokeSessions,
  onOpenWeb
}: {
  state: AccountState | null;
  busy: string;
  onVerifyEmail: () => void;
  onVerifyPhone: () => void;
  onEnableTwoFactor: () => void;
  onDisableTwoFactor: () => void;
  onGenerateRecoveryCodes: () => void;
  onReauthenticate: () => void;
  onRevokeSessions: () => void;
  onOpenWeb: (path?: string) => void;
}) {
  const security = state?.security || {};
  const score = Math.max(0, Math.min(100, Number(security.score || 0)));
  return (
    <>
      <Panel>
        <View style={styles.scoreRow}>
          <View>
            <Text style={styles.panelTitle}>Security score</Text>
            <Text style={styles.muted}>{security.label || "PulseSoc protection"} · {security.trust_level || "standard"}</Text>
          </View>
          <Text style={styles.scoreText}>{score}</Text>
        </View>
        <View style={styles.scoreTrack}>
          <View style={[styles.scoreFill, { width: `${score}%` }]} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Verification</Text>
        <SecurityRow title="Email" detail={security.email || "Not added"} state={security.email_verified ? "Verified" : "Not verified"} onPress={onVerifyEmail} button="Verify email" busy={busy === "verify-email"} />
        <SecurityRow title="Phone" detail={security.phone || "Not added"} state={security.phone_verified ? "Verified" : "Not verified"} onPress={onVerifyPhone} button="Verify phone" busy={busy === "verify-phone"} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Sensitive-action protection</Text>
        <SecurityRow
          title="Two-factor"
          detail={security.two_factor_enabled ? "Enabled for sensitive actions" : "Not enabled"}
          state={security.two_factor_enabled ? "Enabled" : "Available"}
          onPress={security.two_factor_enabled ? onDisableTwoFactor : onEnableTwoFactor}
          button={security.two_factor_enabled ? "Disable 2FA" : "Enable 2FA"}
          busy={busy === "enable-2fa" || busy === "disable-2fa"}
        />
        <SecurityRow
          title="Recovery codes"
          detail={security.recovery_codes_ready ? "Recovery codes generated" : "Generate codes and save them once."}
          state={security.recovery_codes_ready ? "Saved" : "Needed"}
          onPress={onGenerateRecoveryCodes}
          button="Generate codes"
          busy={busy === "recovery-codes"}
        />
        <View style={styles.buttonGrid}>
          <ActionButton label="Reauthenticate" variant="secondary" disabled={busy === "reauthenticate"} onPress={onReauthenticate} />
          <ActionButton label="Sign out other sessions" variant="danger" disabled={busy === "revoke-sessions"} onPress={onRevokeSessions} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Security history</Text>
        {(state?.securityEvents || []).slice(0, 6).map((event) => (
          <View key={event.id} style={styles.eventRow}>
            <Text style={styles.eventTitle}>{humanize(event.event_type)}</Text>
            <Text style={styles.muted}>{event.device_label || "PulseSoc"} · {formatShortTime(event.created_at)}</Text>
          </View>
        ))}
        {!state?.securityEvents.length ? <Text style={styles.muted}>No recent security events were returned by the backend.</Text> : null}
        <ActionButton label="Advanced security web" variant="secondary" onPress={() => onOpenWeb("/dashboard/account/security")} />
      </Panel>
    </>
  );
}

function PrivacySectionView({
  draftSettings,
  busy,
  onDraftChange,
  onSave,
  onOpenWeb
}: {
  draftSettings: AccountSettings;
  busy: string;
  onDraftChange: (key: string, value: string) => void;
  onSave: () => void;
  onOpenWeb: (path?: string) => void;
}) {
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>Privacy controls</Text>
        <SettingChoice label="Profile visibility" settingKey="profile_visibility" value={draftSettings.profile_visibility} onChange={onDraftChange} />
        <SettingChoice label="Message requests" settingKey="message_requests" value={draftSettings.message_requests} onChange={onDraftChange} />
        <SettingChoice label="Status replies" settingKey="status_replies" value={draftSettings.status_replies} onChange={onDraftChange} />
        <SettingChoice label="Ads personalization" settingKey="ads_personalization" value={draftSettings.ads_personalization} onChange={onDraftChange} />
        <ActionButton label={busy === "save-settings" ? "Saving" : "Save privacy settings"} disabled={busy === "save-settings"} onPress={onSave} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Notification privacy</Text>
        <SettingChoice label="Notifications" settingKey="notifications_enabled" value={draftSettings.notifications_enabled} onChange={onDraftChange} />
        <Text style={styles.muted}>Detailed notification channels remain in the native Notification Preferences screen and existing backend preference APIs.</Text>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>Data controls</Text>
        <Text style={styles.muted}>Data export, deletion, privacy policy, and retention details stay connected to the existing protected web Privacy Center.</Text>
        <ActionButton label="Open Privacy Center" variant="secondary" onPress={() => onOpenWeb("/privacy-center")} />
      </Panel>
    </>
  );
}

function DevicesSectionView({
  state,
  busy,
  onRemove,
  onOpenWeb
}: {
  state: AccountState | null;
  busy: string;
  onRemove: (deviceId: number) => void;
  onOpenWeb: (path?: string) => void;
}) {
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>Sessions and devices</Text>
        <View style={styles.metricGrid}>
          <Metric label="Trusted devices" value={String(state?.security.trusted_devices_count ?? state?.trustedDevices.length ?? 0)} />
          <Metric label="Active sessions" value={String(state?.security.active_sessions_count ?? 0)} />
          <Metric label="Suspicious alerts" value={String(state?.security.suspicious_alerts ?? 0)} />
        </View>
      </Panel>
      <Panel>
        <Text style={styles.panelTitle}>Trusted devices</Text>
        {(state?.trustedDevices || []).map((device) => (
          <View key={device.id} style={styles.deviceRow}>
            <View style={styles.deviceCopy}>
              <Text style={styles.deviceTitle}>{device.device_label || "Trusted device"}</Text>
              <Text style={styles.muted}>Last seen {formatShortTime(device.last_seen_at || device.created_at) || "unknown"}</Text>
            </View>
            <ActionButton label={busy === `remove-device:${device.id}` ? "Removing" : "Remove"} variant="danger" disabled={busy === `remove-device:${device.id}`} onPress={() => onRemove(device.id)} />
          </View>
        ))}
        {!state?.trustedDevices.length ? <Text style={styles.muted}>No trusted devices were returned by the backend.</Text> : null}
        <ActionButton label="Advanced device web" variant="secondary" onPress={() => onOpenWeb("/pulse/settings/devices")} />
      </Panel>
    </>
  );
}

function SettingChoice({
  label,
  settingKey,
  value,
  onChange
}: {
  label: string;
  settingKey: string;
  value?: string;
  onChange: (key: string, value: string) => void;
}) {
  const options = settingOptions[settingKey] || [];
  return (
    <View style={styles.choiceBlock}>
      <Text style={styles.choiceLabel}>{label}</Text>
      <View style={styles.choiceRow}>
        {options.map((option) => (
          <Pressable
            accessibilityRole="button"
            key={option.value}
            style={[styles.choicePill, value === option.value && styles.choicePillActive]}
            onPress={() => onChange(settingKey, option.value)}
          >
            <Text style={[styles.choiceText, value === option.value && styles.choiceTextActive]}>{option.label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

function SecurityRow({
  title,
  detail,
  state,
  button,
  busy,
  onPress
}: {
  title: string;
  detail: string;
  state: string;
  button: string;
  busy: boolean;
  onPress: () => void;
}) {
  return (
    <View style={styles.securityRow}>
      <View style={styles.securityCopy}>
        <Text style={styles.deviceTitle}>{title}</Text>
        <Text style={styles.muted}>{detail}</Text>
      </View>
      <View style={styles.securityAction}>
        <Text style={styles.statusPill}>{state}</Text>
        <ActionButton label={busy ? "Working" : button} disabled={busy} variant="secondary" onPress={onPress} />
      </View>
    </View>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
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
  variant?: "primary" | "secondary" | "danger";
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      style={[styles.actionButton, variant === "secondary" && styles.secondaryButton, variant === "danger" && styles.dangerButton, disabled && styles.disabled]}
      onPress={onPress}
    >
      <Text style={[styles.actionText, variant !== "primary" && styles.secondaryText]}>{label}</Text>
    </Pressable>
  );
}

function normalizeSection(section?: string): AccountSection {
  if (section === "security" || section === "privacy" || section === "devices") return section;
  return "account";
}

function sectionTitle(section: AccountSection) {
  if (section === "security") return "Security Center";
  if (section === "privacy") return "Privacy Center";
  if (section === "devices") return "Sessions and Devices";
  return "Account Center";
}

function humanize(value?: string) {
  return String(value || "Security event").replace(/_/g, " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  content: {
    gap: 14,
    padding: 16
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
  header: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    overflow: "hidden",
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
  sectionTabs: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  sectionTab: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 38,
    justifyContent: "center",
    paddingHorizontal: 13
  },
  sectionTabActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  sectionTabText: {
    color: colors.text,
    fontWeight: "800"
  },
  sectionTabTextActive: {
    color: "#08110f"
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
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  metric: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minWidth: 104,
    padding: 10
  },
  metricValue: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    marginTop: 2
  },
  choiceBlock: {
    gap: 8
  },
  choiceLabel: {
    color: colors.text,
    fontWeight: "800"
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
  dangerButton: {
    backgroundColor: "rgba(255,107,107,0.12)",
    borderColor: colors.danger,
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
  buttonGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  scoreRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12
  },
  scoreText: {
    color: colors.accent,
    fontSize: 34,
    fontWeight: "900"
  },
  scoreTrack: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 999,
    height: 10,
    overflow: "hidden"
  },
  scoreFill: {
    backgroundColor: colors.accent,
    height: 10
  },
  securityRow: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 10
  },
  securityCopy: {
    gap: 3
  },
  securityAction: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    justifyContent: "space-between"
  },
  statusPill: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  eventRow: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 10
  },
  eventTitle: {
    color: colors.text,
    fontWeight: "800"
  },
  deviceRow: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
    padding: 10
  },
  deviceCopy: {
    flex: 1,
    gap: 3
  },
  deviceTitle: {
    color: colors.text,
    fontWeight: "900"
  }
});
