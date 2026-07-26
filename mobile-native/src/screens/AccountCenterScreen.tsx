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
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props =
  | NativeStackScreenProps<RootStackParamList, "AccountCenter">
  | NativeStackScreenProps<RootStackParamList, "AccountSettings">
  | NativeStackScreenProps<RootStackParamList, "AccountSecurity">
  | NativeStackScreenProps<RootStackParamList, "AccountWebSettings">
  | NativeStackScreenProps<RootStackParamList, "AccountWebSecurity">
  | NativeStackScreenProps<RootStackParamList, "AccountPrivacy">
  | NativeStackScreenProps<RootStackParamList, "AccountDevices">;

type AccountSection = "account" | "security" | "privacy" | "devices";

/**
 * `label` holds a catalog key, not display text — it is resolved with `t` at
 * render time so the tab strip re-labels itself the moment the language
 * changes, instead of freezing whatever language was active when this module
 * loaded.
 */
const sections: { key: AccountSection; label: string }[] = [
  { key: "account", label: "settings:accountCenter.sections.account" },
  { key: "security", label: "settings:accountCenter.sections.security" },
  { key: "privacy", label: "settings:accountCenter.sections.privacy" },
  { key: "devices", label: "settings:accountCenter.sections.devices" }
];

/** Same contract as `sections`: `label` is a catalog key, `value` is API data. */
const settingOptions: Record<string, { label: string; value: string }[]> = {
  profile_visibility: [
    { label: "settings:accountCenter.options.public", value: "public" },
    { label: "settings:accountCenter.options.private", value: "private" }
  ],
  message_requests: [
    { label: "settings:accountCenter.options.everyone", value: "everyone" },
    { label: "settings:accountCenter.options.followers", value: "followers" },
    { label: "settings:accountCenter.options.none", value: "none" }
  ],
  notifications_enabled: [
    { label: "settings:accountCenter.options.enabled", value: "true" },
    { label: "settings:accountCenter.options.disabled", value: "false" }
  ],
  status_replies: [
    { label: "settings:accountCenter.options.everyone", value: "everyone" },
    { label: "settings:accountCenter.options.followers", value: "followers" },
    { label: "settings:accountCenter.options.none", value: "none" }
  ],
  ads_personalization: [
    { label: "settings:accountCenter.options.allowed", value: "true" },
    { label: "settings:accountCenter.options.off", value: "false" }
  ],
  sci_fi_intensity: [
    { label: "settings:accountCenter.options.low", value: "low" },
    { label: "settings:accountCenter.options.medium", value: "medium" },
    { label: "settings:accountCenter.options.high", value: "high" }
  ],
  reduced_motion: [
    { label: "settings:accountCenter.options.system", value: "system" },
    { label: "settings:accountCenter.options.reduce", value: "true" },
    { label: "settings:accountCenter.options.fullMotion", value: "false" }
  ],
  language: [
    { label: "settings:accountCenter.options.english", value: "en" },
    { label: "settings:accountCenter.options.spanish", value: "es" },
    { label: "settings:accountCenter.options.french", value: "fr" },
    { label: "settings:accountCenter.options.haitianCreole", value: "ht" },
    { label: "settings:accountCenter.options.portuguese", value: "pt" }
  ]
};

export function AccountCenterScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const initialSection = normalizeSection(route.name, routeSectionParam(route.params));
  const [section, setSection] = useState<AccountSection>(initialSection);
  const [state, setState] = useState<AccountState | null>(null);
  const [draftSettings, setDraftSettings] = useState<AccountSettings>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const title = useMemo(() => t(sectionTitleKey(section)), [section, t]);

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
      setError(loadError instanceof Error ? loadError.message : t("settings:accountCenter.loadError"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    setSection(normalizeSection(route.name, routeSectionParam(route.params)));
  }, [route.name, route.params]);

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
        Alert.alert(t("settings:accountCenter.recoveryCodesAlertTitle"), codes || result.message || t("settings:accountCenter.recoveryCodesAlertBody"));
      }
      setNotice(result.message || t("settings:accountCenter.securityUpdated"));
      if (options.refresh !== false) await load("refresh");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : t("settings:accountCenter.actionError"));
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
      setNotice(result.message || t("settings:accountCenter.settingsSaved"));
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t("settings:accountCenter.settingsSaveError"));
    } finally {
      setBusy("");
    }
  }

  function confirmTrustedDeviceRemoval(deviceId: number) {
    Alert.alert(t("settings:accountCenter.removeDeviceTitle"), t("settings:accountCenter.removeDeviceBody"), [
      { text: t("settings:accountCenter.cancel"), style: "cancel" },
      {
        text: t("settings:accountCenter.remove"),
        style: "destructive",
        onPress: () => runAction(`remove-device:${deviceId}`, () => removeTrustedDevice(deviceId))
      }
    ]);
  }

  function confirmRevokeSessions() {
    Alert.alert(t("settings:accountCenter.revokeSessionsTitle"), t("settings:accountCenter.revokeSessionsBody"), [
      { text: t("settings:accountCenter.cancel"), style: "cancel" },
      {
        text: t("settings:accountCenter.revoke"),
        style: "destructive",
        onPress: () => runAction("revoke-sessions", revokeAllSessions)
      }
    ]);
  }

  if (loading && !state) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("settings:accountCenter.loading")}</Text>
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
        <Text style={styles.eyebrow}>{t("settings:accountCenter.eyebrow")}</Text>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.subtitle}>
          {offline ? t("settings:accountCenter.offlineSubtitle") : t("settings:accountCenter.subtitle")}
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
            <Text style={[styles.sectionTabText, section === item.key && styles.sectionTabTextActive]}>{t(item.label)}</Text>
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
  const { t } = useTranslation();
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.account.statusTitle")}</Text>
        <View style={styles.metricGrid}>
          <Metric label={t("settings:accountCenter.account.plan")} value={state?.status.access_label || state?.status.subscription_plan || t("settings:accountCenter.account.planFallback")} />
          <Metric label={t("settings:accountCenter.account.language")} value={(state?.status.preferred_language || state?.status.language || "en").toUpperCase()} />
          <Metric label={t("settings:accountCenter.account.telegram")} value={state?.status.telegram_linked ? t("settings:accountCenter.account.telegramLinked") : t("settings:accountCenter.account.telegramNotLinked")} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.account.experienceTitle")}</Text>
        <SettingChoice label={t("settings:accountCenter.account.motion")} settingKey="reduced_motion" value={draftSettings.reduced_motion} onChange={onDraftChange} />
        <SettingChoice label={t("settings:accountCenter.account.visualIntensity")} settingKey="sci_fi_intensity" value={draftSettings.sci_fi_intensity} onChange={onDraftChange} />
        <SettingChoice label={t("settings:accountCenter.account.language")} settingKey="language" value={draftSettings.language} onChange={onDraftChange} />
        <ActionButton label={busy === "save-settings" ? t("settings:accountCenter.saving") : t("settings:accountCenter.account.saveSettings")} disabled={busy === "save-settings"} onPress={onSave} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.account.credentialsTitle")}</Text>
        <Text style={styles.muted}>{t("settings:accountCenter.account.credentialsBody")}</Text>
        <View style={styles.buttonGrid}>
          <ActionButton label={t("settings:accountCenter.account.passwordAndEmail")} variant="secondary" onPress={() => onOpenWeb("/account/settings")} />
          <ActionButton label={t("settings:accountCenter.account.deleteAccount")} variant="danger" onPress={() => onOpenWeb("/account/delete")} />
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
  const { t } = useTranslation();
  const security = state?.security || {};
  const score = Math.max(0, Math.min(100, Number(security.score || 0)));
  return (
    <>
      <Panel>
        <View style={styles.scoreRow}>
          <View>
            <Text style={styles.panelTitle}>{t("settings:accountCenter.security.scoreTitle")}</Text>
            <Text style={styles.muted}>{security.label || t("settings:accountCenter.security.labelFallback")} · {security.trust_level || t("settings:accountCenter.security.trustLevelFallback")}</Text>
          </View>
          <Text style={styles.scoreText}>{score}</Text>
        </View>
        <View style={styles.scoreTrack}>
          <View style={[styles.scoreFill, { width: `${score}%` }]} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.security.verificationTitle")}</Text>
        <SecurityRow title={t("settings:accountCenter.security.email")} detail={security.email || t("settings:accountCenter.security.notAdded")} state={security.email_verified ? t("settings:accountCenter.security.verified") : t("settings:accountCenter.security.notVerified")} onPress={onVerifyEmail} button={t("settings:accountCenter.security.verifyEmail")} busy={busy === "verify-email"} />
        <SecurityRow title={t("settings:accountCenter.security.phone")} detail={security.phone || t("settings:accountCenter.security.notAdded")} state={security.phone_verified ? t("settings:accountCenter.security.verified") : t("settings:accountCenter.security.notVerified")} onPress={onVerifyPhone} button={t("settings:accountCenter.security.verifyPhone")} busy={busy === "verify-phone"} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.security.sensitiveTitle")}</Text>
        <SecurityRow
          title={t("settings:accountCenter.security.twoFactor")}
          detail={security.two_factor_enabled ? t("settings:accountCenter.security.twoFactorDetailOn") : t("settings:accountCenter.security.twoFactorDetailOff")}
          state={security.two_factor_enabled ? t("settings:accountCenter.security.twoFactorStateOn") : t("settings:accountCenter.security.twoFactorStateOff")}
          onPress={security.two_factor_enabled ? onDisableTwoFactor : onEnableTwoFactor}
          button={security.two_factor_enabled ? t("settings:accountCenter.security.disableTwoFactor") : t("settings:accountCenter.security.enableTwoFactor")}
          busy={busy === "enable-2fa" || busy === "disable-2fa"}
        />
        <SecurityRow
          title={t("settings:accountCenter.security.recoveryCodes")}
          detail={security.recovery_codes_ready ? t("settings:accountCenter.security.recoveryCodesDetailReady") : t("settings:accountCenter.security.recoveryCodesDetailNeeded")}
          state={security.recovery_codes_ready ? t("settings:accountCenter.security.recoveryCodesStateReady") : t("settings:accountCenter.security.recoveryCodesStateNeeded")}
          onPress={onGenerateRecoveryCodes}
          button={t("settings:accountCenter.security.generateCodes")}
          busy={busy === "recovery-codes"}
        />
        <View style={styles.buttonGrid}>
          <ActionButton label={t("settings:accountCenter.security.reauthenticate")} variant="secondary" disabled={busy === "reauthenticate"} onPress={onReauthenticate} />
          <ActionButton label={t("settings:accountCenter.security.revokeSessions")} variant="danger" disabled={busy === "revoke-sessions"} onPress={onRevokeSessions} />
        </View>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.security.historyTitle")}</Text>
        {(state?.securityEvents || []).slice(0, 6).map((event) => (
          <View key={event.id} style={styles.eventRow}>
            <Text style={styles.eventTitle}>{humanize(event.event_type, t("settings:accountCenter.security.eventFallback"))}</Text>
            <Text style={styles.muted}>{event.device_label || "PulseSoc"} · {formatShortTime(event.created_at)}</Text>
          </View>
        ))}
        {!state?.securityEvents.length ? <Text style={styles.muted}>{t("settings:accountCenter.security.noEvents")}</Text> : null}
        <ActionButton label={t("settings:accountCenter.security.advancedWeb")} variant="secondary" onPress={() => onOpenWeb("/dashboard/account/security")} />
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
  const { t } = useTranslation();
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.privacy.controlsTitle")}</Text>
        <SettingChoice label={t("settings:accountCenter.privacy.profileVisibility")} settingKey="profile_visibility" value={draftSettings.profile_visibility} onChange={onDraftChange} />
        <SettingChoice label={t("settings:accountCenter.privacy.messageRequests")} settingKey="message_requests" value={draftSettings.message_requests} onChange={onDraftChange} />
        <SettingChoice label={t("settings:accountCenter.privacy.statusReplies")} settingKey="status_replies" value={draftSettings.status_replies} onChange={onDraftChange} />
        <SettingChoice label={t("settings:accountCenter.privacy.adsPersonalization")} settingKey="ads_personalization" value={draftSettings.ads_personalization} onChange={onDraftChange} />
        <ActionButton label={busy === "save-settings" ? t("settings:accountCenter.saving") : t("settings:accountCenter.privacy.saveSettings")} disabled={busy === "save-settings"} onPress={onSave} />
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.privacy.notificationTitle")}</Text>
        <SettingChoice label={t("settings:accountCenter.privacy.notifications")} settingKey="notifications_enabled" value={draftSettings.notifications_enabled} onChange={onDraftChange} />
        <Text style={styles.muted}>{t("settings:accountCenter.privacy.notificationBody")}</Text>
      </Panel>

      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.privacy.dataTitle")}</Text>
        <Text style={styles.muted}>{t("settings:accountCenter.privacy.dataBody")}</Text>
        <ActionButton label={t("settings:accountCenter.privacy.openPrivacyCenter")} variant="secondary" onPress={() => onOpenWeb("/privacy-center")} />
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
  const { t } = useTranslation();
  return (
    <>
      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.devices.sessionsTitle")}</Text>
        <View style={styles.metricGrid}>
          <Metric label={t("settings:accountCenter.devices.trustedDevices")} value={String(state?.security.trusted_devices_count ?? state?.trustedDevices.length ?? 0)} />
          <Metric label={t("settings:accountCenter.devices.activeSessions")} value={String(state?.security.active_sessions_count ?? 0)} />
          <Metric label={t("settings:accountCenter.devices.suspiciousAlerts")} value={String(state?.security.suspicious_alerts ?? 0)} />
        </View>
      </Panel>
      <Panel>
        <Text style={styles.panelTitle}>{t("settings:accountCenter.devices.trustedDevicesTitle")}</Text>
        {(state?.trustedDevices || []).map((device) => (
          <View key={device.id} style={styles.deviceRow}>
            <View style={styles.deviceCopy}>
              <Text style={styles.deviceTitle}>{device.device_label || t("settings:accountCenter.devices.deviceFallback")}</Text>
              <Text style={styles.muted}>
                {t("settings:accountCenter.devices.lastSeen", {
                  time: formatShortTime(device.last_seen_at || device.created_at) || t("settings:accountCenter.devices.lastSeenUnknown")
                })}
              </Text>
            </View>
            <ActionButton label={busy === `remove-device:${device.id}` ? t("settings:accountCenter.removing") : t("settings:accountCenter.remove")} variant="danger" disabled={busy === `remove-device:${device.id}`} onPress={() => onRemove(device.id)} />
          </View>
        ))}
        {!state?.trustedDevices.length ? <Text style={styles.muted}>{t("settings:accountCenter.devices.noDevices")}</Text> : null}
        <ActionButton label={t("settings:accountCenter.devices.advancedWeb")} variant="secondary" onPress={() => onOpenWeb("/pulse/settings/devices")} />
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
  const { t } = useTranslation();
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
            <Text style={[styles.choiceText, value === option.value && styles.choiceTextActive]}>{t(option.label)}</Text>
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
  const { t } = useTranslation();
  return (
    <View style={styles.securityRow}>
      <View style={styles.securityCopy}>
        <Text style={styles.deviceTitle}>{title}</Text>
        <Text style={styles.muted}>{detail}</Text>
      </View>
      <View style={styles.securityAction}>
        <Text style={styles.statusPill}>{state}</Text>
        <ActionButton label={busy ? t("settings:accountCenter.working") : button} disabled={busy} variant="secondary" onPress={onPress} />
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

function normalizeSection(routeName: string, section?: string): AccountSection {
  if (section === "security" || section === "privacy" || section === "devices") return section;
  if (routeName === "AccountSecurity" || routeName === "AccountWebSecurity") return "security";
  if (routeName === "AccountPrivacy") return "privacy";
  if (routeName === "AccountDevices") return "devices";
  return "account";
}

function routeSectionParam(params: Props["route"]["params"]): AccountSection | undefined {
  if (params && "section" in params) return params.section;
  return undefined;
}

/** Returns a catalog key; the caller resolves it so the header follows the language. */
function sectionTitleKey(section: AccountSection) {
  if (section === "security") return "settings:accountCenter.titles.security";
  if (section === "privacy") return "settings:accountCenter.titles.privacy";
  if (section === "devices") return "settings:accountCenter.titles.devices";
  return "settings:accountCenter.titles.account";
}

/** `fallback` is already-translated copy, so this stays usable outside React. */
function humanize(value: string | undefined, fallback: string) {
  return String(value || fallback).replace(/_/g, " ").replace(/\b\w/g, (match) => match.toUpperCase());
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
