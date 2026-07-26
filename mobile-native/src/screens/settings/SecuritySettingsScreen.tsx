import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Linking, Platform } from "react-native";
import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { SettingsHeader, SettingsSection, SettingsShell } from "../../settings/components/SettingsShell";
import {
  confirm,
  SettingsBadge,
  SettingsRow,
  SettingsSwitch,
  SettingsValue
} from "../../settings/components/SettingsControls";
import { usePreferenceGroup } from "../../settings/store";
import { RootStackParamList } from "../../navigation/types";
import { useAuth } from "../../session/auth";
import { requestPasswordRecovery } from "../../api/auth";
import { AccountSecurity, disableTwoFactor, enableTwoFactor, getAccountSecurity } from "../../api/account";
import {
  BiometricCapability,
  confirmAndEnableBiometricLogin,
  disableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession
} from "../../session/biometricAuth";
import { translate, useTranslation } from "../../i18n";

type Nav = NativeStackNavigationProp<RootStackParamList>;

/**
 * Name the sensor the way the platform names it. Calling a fingerprint reader
 * "Touch ID" on a Pixel is the kind of detail that makes a security screen feel
 * untrustworthy, and trust is the entire product of this screen.
 *
 * Resolved through the non-React `translate` because this is a plain function,
 * not a component — the hook cannot be called here, and the screen re-renders on
 * a language change anyway, so the name is re-read in the new language.
 */
function biometricLabel(capability: BiometricCapability | null): string {
  const kind = capability?.kind ?? "none";
  if (Platform.OS === "android") {
    if (kind === "faceId") return translate("settings:security.unlock.sensorFaceUnlock");
    if (kind === "touchId") return translate("settings:security.unlock.sensorFingerprintUnlock");
    if (kind === "iris") return translate("settings:security.unlock.sensorIrisUnlock");
    return translate("settings:security.unlock.sensorBiometricUnlock");
  }
  if (kind === "touchId") return translate("settings:security.unlock.sensorTouchId");
  if (kind === "iris") return translate("settings:security.unlock.sensorIrisUnlock");
  return translate("settings:security.unlock.sensorFaceId");
}

/** Why the sensor is unusable, phrased as something the user can act on. */
function unavailableCopy(capability: BiometricCapability, label: string): string {
  if (capability.reason === "no_hardware") {
    return translate("settings:security.unlock.unavailableNoHardware");
  }
  if (capability.reason === "not_enrolled") {
    return translate("settings:security.unlock.unavailableNotEnrolled", { label });
  }
  return translate("settings:security.unlock.unavailableGeneric", { label });
}

/**
 * Security.
 *
 * Three different sources of truth meet here, and the screen keeps them
 * distinct rather than pretending they are one setting:
 *  - the server owns 2FA (`/api/account/security`),
 *  - the device keychain owns biometric enrolment (`session/biometricAuth`),
 *  - the preference store owns the advisory toggles (alerts, re-auth prompts).
 *
 * Server- and device-owned rows are therefore written through their own API
 * first and only mirrored into the preference store once the real mutation
 * succeeds — otherwise an optimistic switch would claim 2FA is on when the
 * account is still unprotected.
 */
export function SecuritySettingsScreen() {
  const navigation = useNavigation<Nav>();
  const { t } = useTranslation();
  const { authState } = useAuth();
  const { value, setGroup, pending } = usePreferenceGroup("security");
  const currentUserId = Number(authState.user?.user_id || 0);

  const [security, setSecurity] = useState<AccountSecurity | null>(null);
  const [loadingSecurity, setLoadingSecurity] = useState(true);
  const [twoFactorBusy, setTwoFactorBusy] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);

  const [capability, setCapability] = useState<BiometricCapability | null>(null);
  const [biometricEnabled, setBiometricEnabled] = useState(false);
  const [biometricBusy, setBiometricBusy] = useState(false);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /**
   * The loaders below need to *read* the current preference to decide whether
   * the server disagrees with it, but must not *depend* on it — depending on it
   * would re-run the fetch on every toggle and fight the user's own change
   * mid-flight. A ref gives them the live value with a stable identity; closing
   * over `value` directly would have pinned them to the first render's snapshot
   * and made a later pull-to-refresh "correct" the switch against a stale
   * comparison.
   */
  const latestValue = useRef(value);
  latestValue.current = value;

  /* ------------------------------ Server state ----------------------------- */

  const loadSecurity = useCallback(async () => {
    try {
      const remote = await getAccountSecurity();
      if (!mounted.current) return;
      setSecurity(remote);
      // The account is authoritative for 2FA. If the cached preference drifted
      // (another device, an admin action), correct it rather than showing a
      // switch that disagrees with the account.
      if (Boolean(remote.two_factor_enabled) !== latestValue.current.twoFactorEnabled) {
        void setGroup({ twoFactorEnabled: Boolean(remote.two_factor_enabled) });
      }
    } catch {
      // Reads must never break the screen — the local mirror still renders, and
      // the row explains that we're showing the last known state.
      if (mounted.current) setSecurity(null);
    } finally {
      if (mounted.current) setLoadingSecurity(false);
    }
  }, [setGroup]);

  const loadBiometrics = useCallback(async () => {
    const [nextCapability, enabled] = await Promise.all([
      getBiometricCapability(),
      isBiometricEnabledForCurrentSession()
    ]).catch(() => [null, false] as [BiometricCapability | null, boolean]);
    if (!mounted.current) return;
    setCapability(nextCapability);
    setBiometricEnabled(enabled);
    // The keychain is the truth for biometrics; the preference is only a synced
    // mirror so other surfaces can read it without touching SecureStore.
    if (enabled !== latestValue.current.biometricUnlock) void setGroup({ biometricUnlock: enabled });
  }, [setGroup]);

  useEffect(() => {
    void loadSecurity();
    void loadBiometrics();
  }, [loadSecurity, loadBiometrics]);

  const refresh = useCallback(async () => {
    await Promise.all([loadSecurity(), loadBiometrics()]);
  }, [loadSecurity, loadBiometrics]);

  /* ------------------------------- Password -------------------------------- */

  const accountEmail = String(security?.email || authState.user?.email || "").trim();

  /**
   * Kept out of the alert's button array so the route name and its header title
   * stay one readable statement rather than being buried in a nested literal.
   */
  const openAccountSection = useCallback(() => {
    navigation.navigate("AccountCenter", { section: "account", title: t("settings:security.password.accountScreenTitle") });
  }, [navigation, t]);

  /**
   * PulseSoc has no authenticated "change password" mutation — the only real
   * path is the emailed reset link. Sending that link is a genuine backend
   * action and, unlike an in-app form, it never puts the password on this
   * device at all.
   */
  const changePassword = useCallback(async () => {
    if (passwordBusy) return;
    if (!accountEmail) {
      Alert.alert(
        t("settings:security.password.noEmailTitle"),
        t("settings:security.password.noEmailBody"),
        [
          { text: t("settings:security.password.noEmailCancel"), style: "cancel" },
          { text: t("settings:security.password.noEmailOpenAccount"), onPress: openAccountSection }
        ]
      );
      return;
    }
    const ok = await confirm({
      title: t("settings:security.password.confirmTitle"),
      message: t("settings:security.password.confirmMessage", { email: accountEmail }),
      confirmLabel: t("settings:security.password.confirmLabel")
    });
    if (!ok) return;
    setPasswordBusy(true);
    try {
      await requestPasswordRecovery(accountEmail);
      Alert.alert(
        t("settings:security.password.sentTitle"),
        t("settings:security.password.sentBody", { email: accountEmail })
      );
    } catch (error) {
      Alert.alert(
        t("settings:security.password.failedTitle"),
        error instanceof Error ? error.message : t("settings:security.password.failedBody")
      );
    } finally {
      if (mounted.current) setPasswordBusy(false);
    }
  }, [accountEmail, openAccountSection, passwordBusy, t]);

  /* --------------------------------- 2FA ----------------------------------- */

  const twoFactorOn = value.twoFactorEnabled;

  const toggleTwoFactor = useCallback(
    async (next: boolean) => {
      if (twoFactorBusy) return;
      if (!next) {
        // Turning protection off is the dangerous direction — make it deliberate.
        const ok = await confirm({
          title: t("settings:security.twoFactor.disableConfirmTitle"),
          message: t("settings:security.twoFactor.disableConfirmMessage"),
          confirmLabel: t("settings:security.twoFactor.disableConfirmLabel"),
          destructive: true
        });
        if (!ok) return;
      }
      setTwoFactorBusy(true);
      try {
        const response = next ? await enableTwoFactor() : await disableTwoFactor();
        if (!mounted.current) return;
        // Only mirror into preferences after the account actually changed.
        await setGroup({ twoFactorEnabled: next });
        setSecurity((current) => (current ? { ...current, two_factor_enabled: next } : current));
        Alert.alert(
          next ? t("settings:security.twoFactor.enabledTitle") : t("settings:security.twoFactor.disabledTitle"),
          String(
            response?.message ||
              (next ? t("settings:security.twoFactor.enabledBody") : t("settings:security.twoFactor.disabledBody"))
          )
        );
      } catch (error) {
        if (!mounted.current) return;
        Alert.alert(
          next
            ? t("settings:security.twoFactor.enableFailedTitle")
            : t("settings:security.twoFactor.disableFailedTitle"),
          error instanceof Error ? error.message : t("settings:security.twoFactor.mutationFailedBody")
        );
      } finally {
        if (mounted.current) setTwoFactorBusy(false);
      }
    },
    [setGroup, t, twoFactorBusy]
  );

  /* ------------------------------- Biometrics ------------------------------ */

  const label = biometricLabel(capability);

  const toggleBiometric = useCallback(
    async (next: boolean) => {
      if (biometricBusy || !currentUserId) return;
      setBiometricBusy(true);
      try {
        if (!next) {
          const ok = await confirm({
            title: t("settings:security.unlock.disableConfirmTitle", { label }),
            message: t("settings:security.unlock.disableConfirmMessage"),
            confirmLabel: t("settings:security.unlock.disableConfirmLabel"),
            destructive: true
          });
          if (!ok) return;
          await disableBiometricLogin().catch(() => undefined);
          if (!mounted.current) return;
          setBiometricEnabled(false);
          await setGroup({ biometricUnlock: false });
          return;
        }

        // Re-read capability at the moment of the tap: the user may have just
        // enrolled a face in OS settings and come back without remounting.
        const fresh = await getBiometricCapability();
        if (!mounted.current) return;
        setCapability(fresh);
        if (!fresh.available) {
          Alert.alert(t("settings:security.unlock.unavailableTitle", { label }), unavailableCopy(fresh, label));
          return;
        }

        const enabled = await confirmAndEnableBiometricLogin(currentUserId).catch(() => false);
        if (!mounted.current) return;
        setBiometricEnabled(enabled);
        await setGroup({ biometricUnlock: enabled });
        Alert.alert(
          enabled
            ? t("settings:security.unlock.enabledTitle", { label })
            : t("settings:security.unlock.notEnabledTitle", { label }),
          enabled
            ? t("settings:security.unlock.enabledBody", { label })
            : t("settings:security.unlock.notEnabledBody")
        );
      } finally {
        if (mounted.current) setBiometricBusy(false);
      }
    },
    [biometricBusy, currentUserId, label, setGroup, t]
  );

  const biometricUnavailable = capability !== null && !capability.available;

  return (
    <SettingsShell bottomDock={false} onRefresh={refresh}>
      <SettingsHeader title={t("settings:security.title")} subtitle={t("settings:security.subtitle")} />

      <SettingsSection
        title={t("settings:security.password.sectionTitle")}
        footnote={t("settings:security.password.sectionFootnote")}
      >
        <SettingsRow
          testID="security-change-password"
          title={t("settings:security.password.rowTitle")}
          subtitle={
            accountEmail
              ? t("settings:security.password.rowSubtitle", { email: accountEmail })
              : t("settings:security.password.rowSubtitleNoEmail")
          }
          icon="key-outline"
          chevron
          busy={passwordBusy}
          onPress={changePassword}
          accessibilityRole="button"
          accessibilityHint={t("settings:security.password.rowHint")}
        />
      </SettingsSection>

      <SettingsSection
        title={t("settings:security.twoFactor.sectionTitle")}
        busy={loadingSecurity || twoFactorBusy}
        footnote={
          security === null && !loadingSecurity
            ? t("settings:security.twoFactor.sectionFootnoteUnreachable")
            : t("settings:security.twoFactor.sectionFootnote")
        }
      >
        <SettingsRow
          testID="security-two-factor-status"
          title={t("settings:security.twoFactor.statusTitle")}
          subtitle={
            twoFactorOn
              ? t("settings:security.twoFactor.statusOnSubtitle")
              : t("settings:security.twoFactor.statusOffSubtitle")
          }
          icon="shield-checkmark-outline"
          accessory={
            <SettingsBadge
              label={twoFactorOn ? t("settings:security.twoFactor.badgeOn") : t("settings:security.twoFactor.badgeOff")}
              tone={twoFactorOn ? "accent" : "warning"}
            />
          }
        />
        <SettingsSwitch
          testID="security-two-factor-toggle"
          title={t("settings:security.twoFactor.toggleTitle")}
          subtitle={t("settings:security.twoFactor.toggleSubtitle")}
          icon="lock-closed-outline"
          value={twoFactorOn}
          busy={twoFactorBusy}
          disabled={loadingSecurity || twoFactorBusy}
          onValueChange={(next) => void toggleTwoFactor(next)}
        />
      </SettingsSection>

      <SettingsSection
        title={t("settings:security.unlock.sectionTitle")}
        footnote={t("settings:security.unlock.sectionFootnote")}
      >
        {biometricUnavailable && capability ? (
          <SettingsRow
            testID="security-biometric-unavailable"
            title={label}
            subtitle={unavailableCopy(capability, label)}
            icon="finger-print-outline"
            accessory={
              <SettingsBadge
                label={
                  capability.reason === "no_hardware"
                    ? t("settings:security.unlock.badgeNoSensor")
                    : t("settings:security.unlock.badgeNotSetUp")
                }
                tone="muted"
              />
            }
            chevron={capability.reason === "not_enrolled"}
            // Nothing to toggle here, but "open the place where you can fix it"
            // is still a real action — better than a disabled row with no exit.
            onPress={
              capability.reason === "not_enrolled"
                ? () => {
                    void Linking.openSettings().catch(() =>
                      Alert.alert(
                        t("settings:security.unlock.openSettingsFailedTitle"),
                        t("settings:security.unlock.openSettingsFailedBody", { label })
                      )
                    );
                  }
                : undefined
            }
            accessibilityRole={capability.reason === "not_enrolled" ? "button" : "none"}
            accessibilityHint={capability.reason === "not_enrolled" ? t("settings:security.unlock.enrolHint") : undefined}
          />
        ) : (
          <SettingsSwitch
            testID="security-biometric-toggle"
            title={t("settings:security.unlock.toggleTitle", { label })}
            subtitle={
              biometricEnabled
                ? t("settings:security.unlock.toggleSubtitleOn", { label })
                : t("settings:security.unlock.toggleSubtitleOff")
            }
            icon="finger-print-outline"
            value={biometricEnabled}
            busy={biometricBusy}
            disabled={biometricBusy || !currentUserId || capability === null}
            onValueChange={(next) => void toggleBiometric(next)}
          />
        )}
      </SettingsSection>

      <SettingsSection title={t("settings:security.alerts.sectionTitle")} busy={pending}>
        <SettingsSwitch
          testID="security-login-alerts"
          title={t("settings:security.alerts.loginAlertsTitle")}
          subtitle={t("settings:security.alerts.loginAlertsSubtitle")}
          icon="notifications-outline"
          value={value.loginAlerts}
          onValueChange={(next) => void setGroup({ loginAlerts: next })}
        />
        <SettingsSwitch
          testID="security-require-password"
          title={t("settings:security.alerts.requirePasswordTitle")}
          subtitle={t("settings:security.alerts.requirePasswordSubtitle")}
          icon="shield-outline"
          value={value.requirePasswordForSensitiveChanges}
          onValueChange={(next) => void setGroup({ requirePasswordForSensitiveChanges: next })}
        />
      </SettingsSection>

      <SettingsSection title={t("settings:security.sessions.sectionTitle")}>
        <SettingsRow
          testID="security-open-sessions"
          title={t("settings:security.sessions.rowTitle")}
          subtitle={t("settings:security.sessions.rowSubtitle")}
          icon="phone-portrait-outline"
          chevron
          accessory={
            security?.active_sessions_count ? (
              <SettingsValue>
                {t("settings:security.sessions.activeCount", { count: security.active_sessions_count })}
              </SettingsValue>
            ) : undefined
          }
          onPress={() => navigation.navigate("SessionsDevices")}
          accessibilityRole="button"
        />
      </SettingsSection>
    </SettingsShell>
  );
}
